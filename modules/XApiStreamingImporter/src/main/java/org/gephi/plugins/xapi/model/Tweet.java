package org.gephi.plugins.xapi.model;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;
import java.util.Collections;
import java.util.List;

@JsonIgnoreProperties(ignoreUnknown = true)
public class Tweet {

    @JsonProperty("id")
    public String id;

    @JsonProperty("author_id")
    public String authorId;

    @JsonProperty("text")
    public String text;

    @JsonProperty("created_at")
    public String createdAt;

    @JsonProperty("entities")
    public Entities entities;

    @JsonProperty("referenced_tweets")
    public List<ReferencedTweet> referencedTweets;

    public List<Mention> mentions() {
        if (entities == null || entities.mentions == null) {
            return Collections.emptyList();
        }
        return entities.mentions;
    }

    public List<ReferencedTweet> referenced() {
        return referencedTweets == null ? Collections.emptyList() : referencedTweets;
    }
}
